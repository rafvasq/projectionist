"""
services/letterboxd.py
Letterboxd scraping service with pagination, FlareSolverr Cloudflare bypass,
and deduplicated list confirmation prompts.
"""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
from typing import List, Optional, Tuple

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}

FLARESOLVERR_ENDPOINTS = [
    "http://host.docker.internal:8191/v1",
    "http://localhost:8191/v1",
]


def build_list_prompt(list_title: str, film_count: int) -> str:
    """
    Build the standard WhatsApp confirmation prompt for a discovered Letterboxd list.
    Eliminates prompt triplication across bot link handlers.
    """
    clean_title = (list_title or "Letterboxd List").strip()
    return (
        f"📋 Found Letterboxd list: *{clean_title}* ({film_count} films).\n\n"
        f"Reply *1* to Download & create Plex Collection\n"
        f"Reply *2* to Download only (no collection)\n"
        f"❌ Reply *0* to Cancel"
    )


def fetch_letterboxd_html(
    url: str,
    session: Optional[requests.Session] = None,
    timeout: float = 15.0,
    flaresolverr_timeout: float = 30.0,
) -> Tuple[Optional[str], str]:
    """
    Fetches HTML for a Letterboxd URL, with retry and FlareSolverr fallback if Cloudflare blocks.

    Returns:
        Tuple of (html_content, resolved_url).
        If fetching fails, html_content is None and resolved_url is the requested url.
    """
    s = session or requests.Session()

    # 1. Direct request with retry
    for attempt in range(2):
        try:
            resp = s.get(url, headers=DEFAULT_HEADERS, timeout=timeout)
            if resp.status_code == 200:
                return resp.text, str(resp.url)
            logger.debug(
                "Direct request attempt %d for %s returned status %d",
                attempt + 1,
                url,
                resp.status_code,
            )
        except Exception as e:
            logger.debug("Direct request attempt %d error for %s: %s", attempt + 1, url, e)
        time.sleep(1)

    # 2. FlareSolverr fallback (if running in Docker or locally)
    for host in FLARESOLVERR_ENDPOINTS:
        try:
            r = requests.post(
                host,
                json={"cmd": "request.get", "url": url},
                timeout=flaresolverr_timeout,
            )
            if r.status_code == 200:
                solution = r.json().get("solution", {})
                html = solution.get("response", "")
                final_url = solution.get("url", url)
                if html:
                    logger.info("Successfully fetched %s via FlareSolverr at %s", url, host)
                    return html, str(final_url)
        except Exception as e:
            logger.debug("FlareSolverr attempt failed at %s for %s: %s", host, url, e)

    logger.warning("Failed to fetch HTML for %s via direct and FlareSolverr channels", url)
    return None, url


def scrape_letterboxd_list(
    url: str,
    session: Optional[requests.Session] = None,
) -> Tuple[str, List[str]]:
    """
    Scrapes a Letterboxd list URL (handling redirects, og metadata, and multi-page pagination).
    Returns (list_title, list_of_movie_titles).
    """
    s = session or requests.Session()
    current_url: Optional[str] = url
    titles: List[str] = []
    list_title: Optional[str] = None
    seen_urls: set[str] = set()

    while current_url and current_url not in seen_urls:
        seen_urls.add(current_url)
        html, resolved_url = fetch_letterboxd_html(current_url, session=s)
        if not html:
            break

        soup = BeautifulSoup(html, "html.parser")

        # Extract title from the first page
        if not list_title:
            og_title = soup.find("meta", property="og:title")
            if og_title and og_title.get("content"):
                raw = str(og_title["content"])
                raw = re.sub(r"\s*•\s*Letterboxd$", "", raw)
                raw = re.sub(r",\s*a list of films by .*$", "", raw, flags=re.IGNORECASE)
                raw = re.sub(r",\s*a list by .*$", "", raw, flags=re.IGNORECASE)
                list_title = raw.strip()
            elif soup.find("h1"):
                h1_el = soup.find("h1")
                if h1_el:
                    list_title = h1_el.get_text(strip=True)
            if not list_title:
                list_title = "Letterboxd List"

        # Extract films on current page
        page_titles: List[str] = []
        lazy_posters = soup.select('.react-component[data-component-class="LazyPoster"], [data-item-name]')
        if lazy_posters:
            for comp in lazy_posters:
                raw_name = comp.get("data-item-name") or comp.get("data-item-full-display-name") or ""
                raw_name = str(raw_name).strip()
                if raw_name and raw_name not in titles and raw_name not in page_titles:
                    page_titles.append(raw_name)
        else:
            for img in soup.select(".poster img, .film-poster img"):
                alt = img.get("alt")
                if alt:
                    clean_alt = str(alt).strip()
                    if clean_alt and clean_alt not in titles and clean_alt not in page_titles:
                        page_titles.append(clean_alt)

        titles.extend(page_titles)

        # Check for next page link
        next_link = soup.select_one("a.next")
        if next_link and next_link.get("href"):
            current_url = urllib.parse.urljoin(resolved_url, str(next_link["href"]))
        else:
            break

    return list_title or "Letterboxd List", titles


def extract_letterboxd_film_metadata(html: str) -> Tuple[Optional[int], Optional[str]]:
    """
    Extract TMDB ID and movie title from a single Letterboxd film HTML page.
    Returns (tmdb_id, title).
    """
    soup = BeautifulSoup(html, "html.parser")
    og_type = soup.find("meta", property="og:type")
    if og_type and og_type.get("content") == "video.movie":
        body = soup.find("body")
        raw_tmdb_id = body.get("data-tmdb-id") if body else None
        tmdb_id: Optional[int] = None
        if raw_tmdb_id:
            try:
                tmdb_id = int(str(raw_tmdb_id).strip())
            except (ValueError, TypeError):
                tmdb_id = None

        meta_title = soup.find("meta", property="og:title")
        title = meta_title.get("content") if meta_title else None
        if title:
            title = re.sub(r"\s*•\s*Letterboxd$", "", str(title)).strip()
        return tmdb_id, title or "Unknown Film"

    return None, None
