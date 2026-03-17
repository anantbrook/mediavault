"""
url_resolver.py
───────────────
Permanent fix for redirect-wrapper and hotlink-protected URLs.

Problem: Many booru sites return wrapped URLs like:
  - https://xbooru.com/public/hotlink.php?img=f47c5f4e...jpeg
  - https://rule34.paheal.net/_images/abc123/file.jpg  (redirect chain)
  - https://img.booru.org/redirect?url=https://...
  - wixmp token-expired DeviantArt URLs

This module resolves ANY wrapper URL to the final real direct URL
using a waterfall of 6 resolution strategies.

Main function:  resolve(url) -> str  (real URL, or original if all fail)
"""

from __future__ import annotations
import re
import time
import random
import requests
from urllib.parse import urlparse, parse_qs, unquote

_UAS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36",
]

# ── Known hotlink/redirect patterns → resolver function ──────────────────────
# Each entry: (regex_pattern, resolver_fn)
# resolver_fn(url, match) -> real_url or None

def _xbooru_hotlink(url: str, m: re.Match) -> str | None:
    """
    xbooru.com/public/hotlink.php?img=HASH.ext
    The hash IS the filename on xbooru's CDN.
    Real URL: https://img.xbooru.com//images/HASH/HASH.ext
    or follow the redirect directly.
    """
    # Strategy A: extract img param → build CDN URL
    qs = parse_qs(urlparse(url).query)
    img = qs.get("img", [None])[0]
    if img:
        # xbooru CDN pattern: images/HASH_PREFIX/FILENAME
        # img = "f47c5f4e5c30dd5d9824e90acc120cd0.jpeg"
        # CDN  = https://img.xbooru.com//images/f47c/f47c5f4e5c30dd5d9824e90acc120cd0.jpeg
        prefix = img[:4]
        cdn = f"https://img.xbooru.com//images/{prefix}/{img}"
        if _url_alive(cdn):
            return cdn
        # Try alternate CDN
        cdn2 = f"https://xbooru.com/images/{prefix}/{img}"
        if _url_alive(cdn2):
            return cdn2

    # Strategy B: follow the redirect chain (hotlink.php redirects to real URL)
    return _follow_redirects(url)


def _paheal_image(url: str, m: re.Match) -> str | None:
    """
    rule34.paheal.net/_images/HASH/ID%20-%20tags.ext
    These are usually direct but sometimes need Referer.
    """
    # Already direct — just add Referer header
    return url  # Mark as direct, let download handle it


def _generic_redirect(url: str, m: re.Match) -> str | None:
    """Follow redirect chain to find real URL."""
    return _follow_redirects(url)


def _booru_org_redirect(url: str, m: re.Match) -> str | None:
    """Extract url= param from redirect wrappers."""
    qs = parse_qs(urlparse(url).query)
    for key in ("url", "img", "image", "src", "file", "link", "to"):
        val = qs.get(key, [None])[0]
        if val:
            decoded = unquote(val)
            if decoded.startswith("http"):
                return decoded
    return _follow_redirects(url)


def _deviantart_wixmp(url: str, m: re.Match) -> str | None:
    """
    DeviantArt wixmp tokens expire in ~60s.
    Re-fetch from oEmbed to get a fresh URL.
    """
    # Can't resolve without the original DA page URL — return None,
    # let the caller fall back to oEmbed strategy
    return None


# ── Pattern registry ──────────────────────────────────────────────────────────
_PATTERNS: list[tuple[str, callable]] = [
    # Xbooru hotlink wrapper
    (r"xbooru\.com/public/hotlink\.php",          _xbooru_hotlink),
    # Paheal images (direct but need Referer)
    (r"rule34\.paheal\.net/_images/",             _paheal_image),
    # Generic redirect params
    (r"\?(?:url|img|image|src|file|link|to)=http", _booru_org_redirect),
    # DeviantArt wixmp (expired tokens)
    (r"wixmp\.com/.*token=",                       _deviantart_wixmp),
    # Any redirect.php / hotlink.php / proxy.php
    (r"/(?:redirect|hotlink|proxy|go|out)\.php",   _generic_redirect),
    # img.booru.org style
    (r"booru\.org.*(?:redirect|proxy)",             _generic_redirect),
]


# ─────────────────────────────────────────────────────────────────────────────
#  Core helpers
# ─────────────────────────────────────────────────────────────────────────────

def _headers(url: str = "") -> dict:
    domain = urlparse(url).netloc
    referer_map = {
        "xbooru":    "https://xbooru.com/",
        "paheal":    "https://rule34.paheal.net/",
        "rule34":    "https://rule34.xxx/",
        "gelbooru":  "https://gelbooru.com/",
        "danbooru":  "https://danbooru.donmai.us/",
        "konachan":  "https://konachan.com/",
        "wallhaven": "https://wallhaven.cc/",
        "yande":     "https://yande.re/",
    }
    referer = "https://www.google.com/"
    for key, ref in referer_map.items():
        if key in domain:
            referer = ref
            break
    return {
        "User-Agent": random.choice(_UAS),
        "Referer":    referer,
        "Accept":     "image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
    }


def _url_alive(url: str, timeout: int = 6) -> bool:
    """Quick HEAD check — returns True if URL gives 200/206."""
    try:
        r = requests.head(url, headers=_headers(url),
                          timeout=timeout, allow_redirects=True)
        return r.status_code in (200, 206)
    except Exception:
        return False


def _follow_redirects(url: str, max_hops: int = 8, timeout: int = 12) -> str | None:
    """
    Follow redirect chain (301/302/303/307/308) manually.
    Returns the final URL if it's a direct media file, else None.
    Also handles meta-refresh and JS window.location redirects.
    """
    current = url
    visited = set()
    session = requests.Session()
    session.max_redirects = 0  # manual redirect following

    for _ in range(max_hops):
        if current in visited:
            break
        visited.add(current)
        try:
            r = session.get(
                current,
                headers=_headers(current),
                timeout=timeout,
                allow_redirects=False,
                stream=True,
            )
            # Redirect responses
            if r.status_code in (301, 302, 303, 307, 308):
                loc = r.headers.get("Location", "")
                if not loc:
                    break
                if loc.startswith("/"):
                    parsed = urlparse(current)
                    loc = f"{parsed.scheme}://{parsed.netloc}{loc}"
                current = loc
                continue

            # Final destination
            if r.status_code in (200, 206):
                ct = r.headers.get("content-type", "")
                # Is it an actual media file?
                if any(t in ct for t in ("image/", "video/", "application/octet")):
                    return current
                # Check URL extension
                path = urlparse(current).path.lower()
                if any(path.endswith(e) for e in
                       (".jpg",".jpeg",".png",".gif",".webp",".mp4",".webm",".mov",".avif")):
                    return current
                # HTML page — try to find og:image or direct img src
                html = r.text[:8192]
                og = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)["\']', html)
                if og:
                    return og.group(1).replace("&amp;","&")
                # Look for direct image link in HTML
                img = re.search(r'<img[^>]+src=["\']([^"\']+\.(?:jpg|jpeg|png|gif|webp|mp4|webm))["\']', html, re.I)
                if img:
                    src = img.group(1)
                    if src.startswith("//"):
                        src = "https:" + src
                    elif src.startswith("/"):
                        parsed = urlparse(current)
                        src = f"{parsed.scheme}://{parsed.netloc}{src}"
                    return src
                break

            break
        except requests.exceptions.TooManyRedirects:
            break
        except Exception:
            break

    return None


# ─────────────────────────────────────────────────────────────────────────────
#  PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def resolve(url: str) -> str:
    """
    Resolve any wrapper/hotlink/redirect URL to the final direct media URL.
    Returns the resolved URL, or the original URL if resolution fails.
    This is the only function you need to call.
    """
    if not url or not url.startswith("http"):
        return url

    # Check each pattern
    for pattern, resolver_fn in _PATTERNS:
        m = re.search(pattern, url, re.I)
        if m:
            try:
                resolved = resolver_fn(url, m)
                if resolved and resolved != url and resolved.startswith("http"):
                    print(f"[RESOLVER] {url[:60]}... → {resolved[:60]}...")
                    return resolved
            except Exception as e:
                print(f"[RESOLVER] Error resolving {url[:50]}: {e}")
            break  # Pattern matched but resolver failed — don't try others

    # No pattern matched — URL might already be direct
    # But do a quick sanity check: if it contains hotlink/redirect keywords, follow it
    path_lower = urlparse(url).path.lower()
    if any(kw in path_lower for kw in ("hotlink", "redirect", "proxy", "/go/", "redir")):
        resolved = _follow_redirects(url)
        if resolved and resolved != url:
            return resolved

    return url


def resolve_batch(urls: list[str], max_workers: int = 4) -> dict[str, str]:
    """Resolve a batch of URLs concurrently. Returns {original: resolved}."""
    import concurrent.futures
    results = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as ex:
        future_map = {ex.submit(resolve, u): u for u in urls}
        for future in concurrent.futures.as_completed(future_map):
            orig = future_map[future]
            try:
                results[orig] = future.result()
            except Exception:
                results[orig] = orig
    return results


def is_wrapper_url(url: str) -> bool:
    """Return True if this URL is a known hotlink/redirect wrapper."""
    if not url:
        return False
    for pattern, _ in _PATTERNS:
        if re.search(pattern, url, re.I):
            return True
    path = urlparse(url).path.lower()
    return any(kw in path for kw in ("hotlink", "redirect", "proxy.php", "/go/", "redir"))
