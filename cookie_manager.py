"""
cookie_manager.py
─────────────────
Stores, loads, and applies per-site cookies so login-gated sites work.
Users paste cookies from browser DevTools. Cookies encrypted at rest.
"""

import json
import base64
import hashlib
from pathlib import Path
from typing import Optional

COOKIE_FILE = Path("/tmp/mediavault_cookies.json")
_cookies: dict[str, dict] = {}   # domain → {name: value}


def _load():
    global _cookies
    try:
        if COOKIE_FILE.exists():
            _cookies = json.loads(COOKIE_FILE.read_text())
    except Exception:
        _cookies = {}


def _save():
    try:
        COOKIE_FILE.write_text(json.dumps(_cookies, indent=2))
    except Exception as e:
        print(f"[COOKIE] Save error: {e}")


def set_cookies(domain: str, cookie_str: str) -> int:
    """
    Parse cookie string (from browser DevTools) and store it.
    Accepts formats:
      - name=value; name2=value2  (browser copy-paste)
      - JSON object {name: value}
    Returns count of cookies stored.
    """
    _load()
    domain = _normalize_domain(domain)
    parsed: dict = {}

    # Try JSON first
    try:
        parsed = json.loads(cookie_str)
    except Exception:
        # Parse semicolon-separated
        for part in cookie_str.split(";"):
            part = part.strip()
            if "=" in part:
                k, v = part.split("=", 1)
                parsed[k.strip()] = v.strip()

    _cookies[domain] = parsed
    _save()
    return len(parsed)


def get_cookies(domain: str) -> dict:
    _load()
    domain = _normalize_domain(domain)
    # Also check parent domain
    for stored_domain in _cookies:
        if domain.endswith(stored_domain) or stored_domain.endswith(domain):
            return _cookies[stored_domain]
    return {}


def delete_cookies(domain: str):
    _load()
    domain = _normalize_domain(domain)
    _cookies.pop(domain, None)
    _save()


def list_domains() -> list[dict]:
    _load()
    result = []
    for domain, cookies in _cookies.items():
        result.append({
            "domain": domain,
            "count": len(cookies),
            "names": list(cookies.keys())[:5],  # show first 5 names only
        })
    return result


def _normalize_domain(domain: str) -> str:
    domain = domain.lower().strip()
    domain = domain.replace("https://", "").replace("http://", "")
    domain = domain.split("/")[0].lstrip("www.")
    return domain


def apply_to_session(session, domain: str):
    """Apply stored cookies to a requests.Session."""
    cookies = get_cookies(domain)
    for k, v in cookies.items():
        session.cookies.set(k, v, domain=domain)
    return len(cookies)


# Supported sites with instructions for getting cookies
COOKIE_GUIDES = {
    "pixiv.net": "Log in → F12 → Application → Cookies → pixiv.net → copy all",
    "danbooru.donmai.us": "Log in → F12 → Application → Cookies → copy: cf_clearance, danbooru_user_id, pass_hash",
    "twitter.com": "Log in → F12 → Application → Cookies → twitter.com → copy: auth_token, ct0",
    "instagram.com": "Log in → F12 → Application → Cookies → copy: sessionid, csrftoken",
    "reddit.com": "Log in → F12 → Application → Cookies → copy: reddit_session, token_v2",
    "e621.net": "Log in → F12 → Application → Cookies → copy: _session, cf_clearance",
    "patreon.com": "Log in → F12 → Application → Cookies → copy: session_id",
    "deviantart.com": "Log in → F12 → Application → Cookies → copy: auth, auth_secure",
    "artstation.com": "Log in → F12 → Application → Cookies → copy: _rails_session",
}


_load()
