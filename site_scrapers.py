"""
site_scrapers.py
────────────────
Site-specific scrapers for platforms that block direct downloads.
Each scraper returns list of {url, title, thumb, tags, score, source}.
"""

import re
import json
import time
import random
import requests
from typing import Optional
from bypass_cloudflare import get_session, make_headers
from cookie_manager import get_cookies

# ─────────────────────────────────────────────────────────────────────────────
#  PIXIV
# ─────────────────────────────────────────────────────────────────────────────
def scrape_pixiv(tag: str, page: int = 1, cookies: Optional[dict] = None) -> list[dict]:
    """
    Scrape Pixiv by tag. Requires login cookies for R18 content.
    Uses Pixiv's AJAX API endpoint.
    """
    session = get_session("pixiv.net")
    if cookies:
        for k, v in cookies.items():
            session.cookies.set(k, v, domain="pixiv.net")
    stored = get_cookies("pixiv.net")
    if stored:
        for k, v in stored.items():
            session.cookies.set(k, v, domain="pixiv.net")

    session.headers.update({
        "Referer": "https://www.pixiv.net/",
        "Accept": "application/json",
        "x-user-id": stored.get("user_id", ""),
    })

    url = f"https://www.pixiv.net/ajax/search/artworks/{requests.utils.quote(tag)}"
    params = {"word": tag, "order": "popular_d", "mode": "all", "p": page,
              "s_mode": "s_tag_full", "type": "all", "lang": "en"}
    try:
        r = session.get(url, params=params, timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
        items = []
        for illust in data.get("body", {}).get("illustManga", {}).get("data", []):
            items.append({
                "url":    f"https://www.pixiv.net/en/artworks/{illust['id']}",
                "direct": illust.get("url", ""),
                "title":  illust.get("title", ""),
                "thumb":  illust.get("url", ""),
                "tags":   [tag],
                "score":  illust.get("bookmarkCount", 0),
                "source": "Pixiv",
                "id":     illust["id"],
            })
        return items
    except Exception as e:
        print(f"[Pixiv] Error: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
#  DEVIANTART (public RSS feed — no login needed)
# ─────────────────────────────────────────────────────────────────────────────
def scrape_deviantart_rss(tag: str, limit: int = 20) -> list[dict]:
    """
    DeviantArt RSS feed — bypasses IP blocks by using public RSS.
    More reliable than API for Railway datacenter IPs.
    """
    import xml.etree.ElementTree as ET
    session = get_session("backend.deviantart.com")
    url = f"https://backend.deviantart.com/rss.xml"
    params = {"q": f"tag:{tag}", "type": "deviation", "results_per_page": min(limit, 60)}
    try:
        r = session.get(url, params=params, timeout=20)
        if r.status_code != 200:
            return []
        root = ET.fromstring(r.content)
        ns = {"media": "http://search.yahoo.com/mrss/",
              "atom": "http://www.w3.org/2005/Atom"}
        items = []
        for item in root.findall(".//item"):
            title = item.findtext("title", "")
            link  = item.findtext("link", "")
            media = item.find("media:content", ns)
            thumb = item.find("media:thumbnail", ns)
            if media is None:
                continue
            media_url = media.get("url", "")
            thumb_url = thumb.get("url", "") if thumb is not None else media_url
            items.append({
                "url":    media_url,
                "title":  title,
                "thumb":  thumb_url,
                "tags":   [tag],
                "score":  0,
                "source": "DeviantArt",
                "page":   link,
            })
        return items
    except Exception as e:
        print(f"[DA-RSS] Error: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
#  ARTSTATION
# ─────────────────────────────────────────────────────────────────────────────
def scrape_artstation(query: str, page: int = 1) -> list[dict]:
    """ArtStation public search API — no login needed for SFW."""
    session = get_session("www.artstation.com")
    url = "https://www.artstation.com/api/v2/assets/search.json"
    params = {"q": query, "page": page, "sorting": "likes_count", "per_page": 20}
    try:
        r = session.get(url, params=params, timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
        items = []
        for asset in data.get("data", []):
            images = asset.get("images", [])
            if not images:
                continue
            img_url = images[0].get("image_url", "")
            items.append({
                "url":    img_url,
                "title":  asset.get("title", ""),
                "thumb":  asset.get("cover_asset_url", img_url),
                "tags":   asset.get("tags", []),
                "score":  asset.get("likes_count", 0),
                "source": "ArtStation",
            })
        return items
    except Exception as e:
        print(f"[ArtStation] Error: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
#  TWITTER/X (no API key — uses public syndication)
# ─────────────────────────────────────────────────────────────────────────────
def scrape_twitter_media(username: str) -> list[dict]:
    """
    Get media from a Twitter/X profile using the syndication API.
    No API key needed. No login needed for public accounts.
    """
    session = get_session("syndication.twitter.com")
    url = f"https://syndication.twitter.com/srv/timeline-profile/screen-name/{username}"
    params = {"limit": 20}
    try:
        r = session.get(url, params=params, timeout=15)
        if r.status_code != 200:
            return []
        data = r.json()
        items = []
        for tweet in data.get("timeline", {}).get("entries", []):
            content = tweet.get("content", {}).get("itemContent", {})
            result  = content.get("tweet_results", {}).get("result", {})
            legacy  = result.get("legacy", {})
            entities = legacy.get("extended_entities", {})
            for media in entities.get("media", []):
                if media.get("type") in ("photo", "video", "animated_gif"):
                    url_m = media.get("media_url_https") or media.get("media_url", "")
                    # For photos get largest size
                    if "?format=" not in url_m:
                        url_m += "?format=jpg&name=large"
                    items.append({
                        "url":    url_m,
                        "title":  legacy.get("full_text", "")[:80],
                        "thumb":  url_m + "?format=jpg&name=small",
                        "tags":   [],
                        "score":  legacy.get("favorite_count", 0),
                        "source": "Twitter",
                    })
        return items
    except Exception as e:
        print(f"[Twitter] Error: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
#  REDDIT
# ─────────────────────────────────────────────────────────────────────────────
def scrape_reddit(subreddit: str, sort: str = "hot", limit: int = 25) -> list[dict]:
    """Scrape subreddit for images/videos using Reddit JSON API."""
    session = get_session("www.reddit.com")
    session.headers["User-Agent"] = "MediaVaultBot/1.0"
    url = f"https://www.reddit.com/r/{subreddit}/{sort}.json"
    params = {"limit": limit, "raw_json": 1}
    try:
        r = session.get(url, params=params, timeout=15)
        if r.status_code != 200:
            return []
        items = []
        for post in r.json().get("data", {}).get("children", []):
            d = post.get("data", {})
            # Image posts
            if d.get("post_hint") == "image":
                items.append({
                    "url":    d.get("url", ""),
                    "title":  d.get("title", "")[:80],
                    "thumb":  d.get("thumbnail", ""),
                    "tags":   [subreddit],
                    "score":  d.get("score", 0),
                    "source": "Reddit",
                })
            # Video posts
            elif d.get("is_video"):
                vid = d.get("media", {}).get("reddit_video", {})
                items.append({
                    "url":    vid.get("fallback_url", ""),
                    "title":  d.get("title", "")[:80],
                    "thumb":  d.get("thumbnail", ""),
                    "tags":   [subreddit],
                    "score":  d.get("score", 0),
                    "source": "Reddit",
                    "is_video": True,
                })
            # Gallery
            elif d.get("is_gallery"):
                for img_id, img_data in (d.get("media_metadata") or {}).items():
                    img_url = f"https://i.redd.it/{img_id}.jpg"
                    items.append({
                        "url":    img_url,
                        "title":  d.get("title", "")[:60],
                        "thumb":  img_url,
                        "tags":   [subreddit],
                        "score":  d.get("score", 0),
                        "source": "Reddit",
                    })
        return items
    except Exception as e:
        print(f"[Reddit] Error: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
#  UNIFIED SCRAPER ROUTER
# ─────────────────────────────────────────────────────────────────────────────
def scrape_site(site: str, query: str, **kwargs) -> list[dict]:
    """Route to correct scraper by site name."""
    site = site.lower()
    if "pixiv" in site:
        return scrape_pixiv(query, **kwargs)
    elif "deviantart" in site:
        return scrape_deviantart_rss(query, **kwargs)
    elif "artstation" in site:
        return scrape_artstation(query, **kwargs)
    elif "twitter" in site or "x.com" in site:
        return scrape_twitter_media(query, **kwargs)
    elif "reddit" in site:
        return scrape_reddit(query, **kwargs)
    return []
