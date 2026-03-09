"""
bypass_cloudflare.py
────────────────────
Handles Cloudflare, anti-hotlink, rate-limit, and login-wall bypasses.
Techniques: realistic browser headers, cookie injection, retry with
backoff, rotating User-Agents, Referer spoofing, session persistence.
"""

import time
import random
import requests
from pathlib import Path
from typing import Optional

# ── Realistic Browser User-Agents (rotate to avoid fingerprinting) ──────────
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.3; rv:123.0) Gecko/20100101 Firefox/123.0",
]

# ── Full browser-like headers per domain ─────────────────────────────────────
DOMAIN_HEADERS = {
    "danbooru.donmai.us": {
        "Referer":        "https://danbooru.donmai.us/",
        "Origin":         "https://danbooru.donmai.us",
        "Accept":         "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language":"en-US,en;q=0.9",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
    },
    "gelbooru.com": {
        "Referer":         "https://gelbooru.com/",
        "Accept":          "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
        "Sec-Fetch-Dest":  "image",
        "Sec-Fetch-Mode":  "no-cors",
        "Sec-Fetch-Site":  "same-site",
    },
    "rule34.xxx": {
        "Referer":         "https://rule34.xxx/",
        "Accept":          "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    },
    "konachan.com": {
        "Referer":         "https://konachan.com/",
        "Accept":          "image/webp,*/*",
    },
    "yande.re": {
        "Referer":         "https://yande.re/",
        "Accept":          "image/webp,*/*",
    },
    "wallhaven.cc": {
        "Referer":         "https://wallhaven.cc/",
        "Accept":          "image/webp,*/*",
    },
    "pbs.twimg.com": {
        "Referer":         "https://twitter.com/",
        "Origin":          "https://twitter.com",
    },
    "i.redd.it": {
        "Referer":         "https://www.reddit.com/",
        "Origin":          "https://www.reddit.com",
    },
    "cdn.discordapp.com": {
        "Referer":         "https://discord.com/",
    },
    "i.pximg.net": {
        "Referer":         "https://www.pixiv.net/",
        "Origin":          "https://www.pixiv.net",
    },
}

# ── Persistent session pool (one session per domain for cookie persistence) ──
_sessions: dict[str, requests.Session] = {}

def get_session(domain: str) -> requests.Session:
    if domain not in _sessions:
        s = requests.Session()
        s.headers.update(make_headers(domain))
        _sessions[domain] = s
    return _sessions[domain]

def make_headers(domain: str, url: str = "") -> dict:
    """Build realistic browser headers for a given domain."""
    base = {
        "User-Agent":      random.choice(USER_AGENTS),
        "Accept-Encoding": "gzip, deflate, br",
        "Connection":      "keep-alive",
        "DNT":             "1",
        "Upgrade-Insecure-Requests": "1",
    }
    # Match domain-specific headers
    for key, hdrs in DOMAIN_HEADERS.items():
        if key in domain:
            base.update(hdrs)
            break
    # Generic Referer fallback — use the domain root
    if "Referer" not in base and domain:
        base["Referer"] = f"https://{domain}/"
    return base

def download_with_bypass(
    url: str,
    dest_path: Path,
    cookies: Optional[dict] = None,
    max_retries: int = 5,
    timeout: int = 60,
) -> tuple[bool, str]:
    """
    Download a URL to dest_path, bypassing common blocks.
    Returns (success: bool, message: str)
    """
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lstrip("www.")

    session = get_session(domain)
    if cookies:
        session.cookies.update(cookies)

    delays = [1, 2, 4, 8, 16]  # exponential backoff

    for attempt in range(max_retries):
        try:
            # Rotate UA on each retry
            session.headers["User-Agent"] = random.choice(USER_AGENTS)

            resp = session.get(url, stream=True, timeout=timeout,
                               allow_redirects=True)

            # Handle specific status codes
            if resp.status_code == 429:
                wait = delays[min(attempt, len(delays)-1)] + random.uniform(0, 2)
                time.sleep(wait)
                continue

            if resp.status_code == 403:
                # Try with different Referer
                session.headers["Referer"] = url.rsplit("/", 1)[0] + "/"
                time.sleep(delays[min(attempt, len(delays)-1)])
                continue

            if resp.status_code == 401:
                return False, "Login required — add cookies in Sources tab"

            if resp.status_code not in (200, 206):
                return False, f"HTTP {resp.status_code}"

            # Stream write
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            total = 0
            with open(dest_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=65536):
                    if chunk:
                        f.write(chunk)
                        total += len(chunk)

            if total < 512:
                return False, f"File too small ({total} bytes) — likely blocked"

            return True, f"OK ({total:,} bytes)"

        except requests.exceptions.ConnectionError:
            time.sleep(delays[min(attempt, len(delays)-1)])
        except requests.exceptions.Timeout:
            time.sleep(2)
        except Exception as e:
            return False, str(e)

    return False, f"Failed after {max_retries} retries"


def inject_cookies_from_browser(domain: str, cookie_str: str):
    """
    Accept a Netscape/browser cookie string and inject into the session.
    Users can paste cookies from browser DevTools → Application → Cookies.
    """
    session = get_session(domain)
    for part in cookie_str.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            session.cookies.set(k.strip(), v.strip(), domain=domain)


def test_bypass(url: str) -> dict:
    """Test if a URL is accessible and return diagnostic info."""
    from urllib.parse import urlparse
    domain = urlparse(url).netloc.lstrip("www.")
    session = get_session(domain)
    result = {"url": url, "domain": domain, "status": None, "blocked": False, "reason": ""}
    try:
        r = session.head(url, timeout=10, allow_redirects=True)
        result["status"] = r.status_code
        if r.status_code == 403:
            result["blocked"] = True
            result["reason"] = "403 Forbidden — anti-hotlink or CF"
        elif r.status_code == 429:
            result["blocked"] = True
            result["reason"] = "429 Rate limited"
        elif r.status_code == 401:
            result["blocked"] = True
            result["reason"] = "401 Login required"
        cf = r.headers.get("cf-ray") or r.headers.get("CF-RAY")
        if cf:
            result["cloudflare"] = True
            result["cf_ray"] = cf
    except Exception as e:
        result["error"] = str(e)
        result["blocked"] = True
    return result
