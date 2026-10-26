"""
services/overseerr.py
Overseerr API adapter service.
Handles media search, title/year fuzzy disambiguation (movie-priority),
metadata detail retrieval, and media request submissions with explicit timeouts.
"""

from __future__ import annotations

import logging
import urllib.parse
from typing import Any, Optional, Union
import requests

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT: float = 10.0


class OverseerrService:
    """
    Adapter client for Overseerr / Jellyseerr v1 REST API.

    Features:
    - Explicit HTTP timeout bounds on all operations (default: 10.0s).
    - Robust error handling returning None/empty results on failure.
    - Title and year matching with movie-priority and miniseries support.
    - Configuration factory `from_config(cfg)`.
    """

    def __init__(
        self,
        url: Optional[str] = None,
        api_key: Optional[str] = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.url = (url or "").rstrip("/")
        self.api_key = api_key or ""
        self.timeout = float(timeout)

    @classmethod
    def from_config(cls, cfg: Optional[dict[str, Any]] = None) -> OverseerrService:
        """Construct OverseerrService from configuration dictionary or default config."""
        if cfg is None:
            from core.config import load_config
            cfg = load_config()

        os_cfg = cfg.get("overseerr", {}) if isinstance(cfg, dict) else {}
        return cls(
            url=os_cfg.get("url", ""),
            api_key=os_cfg.get("api_key", ""),
            timeout=float(os_cfg.get("timeout", DEFAULT_TIMEOUT)),
        )

    @property
    def is_configured(self) -> bool:
        """Check if both URL and API key are configured."""
        return bool(self.url and self.api_key)

    @property
    def _headers(self) -> dict[str, str]:
        """HTTP headers required for Overseerr API authentication."""
        if not self.api_key:
            return {}
        return {"X-Api-Key": self.api_key}

    def search(self, query: str) -> Optional[list[dict[str, Any]]]:
        """
        Search Overseerr for media matching query string.
        Filters results to include only movies and TV shows (excluding people/collections).

        Returns:
            list[dict] on success (may be empty).
            None on HTTP failure, connection error, or missing configuration.
        """
        if not self.is_configured:
            logger.warning("Overseerr search skipped: service is not configured")
            return None

        clean_query = query.strip() if query else ""
        if not clean_query:
            return []

        endpoint = f"{self.url}/api/v1/search"
        try:
            resp = requests.get(
                endpoint,
                # Overseerr rejects '+' for spaces; it requires RFC 3986 percent-encoding.
                params=urllib.parse.urlencode({"query": clean_query}, quote_via=urllib.parse.quote),
                headers=self._headers,
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                data = resp.json()
                results = data.get("results", []) if isinstance(data, dict) else []
                valid_results = [
                    r for r in results
                    if isinstance(r, dict) and r.get("mediaType") in ("movie", "tv")
                ]
                return valid_results

            logger.error(
                "Overseerr search failed for query '%s' with HTTP status %d: %s",
                clean_query,
                resp.status_code,
                resp.text[:200],
            )
            return None

        except requests.RequestException as exc:
            logger.error("Overseerr search network error for query '%s': %s", clean_query, exc)
            return None
        except Exception as exc:
            logger.error("Unexpected error during Overseerr search for query '%s': %s", clean_query, exc)
            return None

    def find_best_match(
        self,
        title: str,
        target_year: Optional[Union[int, str]] = None,
        media_list: Optional[list[dict[str, Any]]] = None,
        allow_tv: bool = False,
    ) -> tuple[Optional[str], Optional[dict[str, Any]]]:
        """
        Find best matching media from Overseerr for title and optional release year.

        Matching priority:
        1. Exact title & exact year (movies first).
        2. Exact title & exact year (TV show/miniseries if allow_tv=True).
        3. Exact title & year +/- 1 (movies, handles festival release difference).
        4. Exact title & year +/- 1 (TV if allow_tv=True).
        5. If target_year was not specified:
           a. Exact title match (movies first).
           b. Exact title match (TV if allow_tv=True).
           c. First movie in results.
           d. First TV show in results (if allow_tv=True).

        Returns:
            (media_type, media_item) where media_type is 'movie' or 'tv', or (None, None).
        """
        if not title or not title.strip():
            return None, None

        if media_list is None:
            media_list = self.search(title)

        if not media_list:
            return None, None

        movies: list[dict[str, Any]] = []
        tv_shows: list[dict[str, Any]] = []

        for r in media_list:
            mtype = r.get("mediaType")
            if not mtype:
                mtype = "movie" if "title" in r else "tv"

            if mtype == "movie":
                movies.append(r)
            elif mtype == "tv" and allow_tv:
                tv_shows.append(r)

        clean_title = title.lower().strip()
        str_year = str(target_year).strip() if target_year is not None and str(target_year).strip() else None

        # 1. Matching with specified target_year
        if str_year:
            # 1. Exact title & exact year (movies first)
            for m in movies:
                m_year = (m.get("releaseDate") or "").split("-")[0]
                m_title = (m.get("title") or "").lower().strip()
                if m_title == clean_title and m_year == str_year:
                    return "movie", m

            if allow_tv:
                # 2. Exact title & exact year (TV miniseries)
                for t in tv_shows:
                    t_year = (t.get("firstAirDate") or "").split("-")[0]
                    t_title = (t.get("name") or "").lower().strip()
                    if t_title == clean_title and t_year == str_year:
                        return "tv", t

            # 3. Exact title & year +/- 1 (movies, handles premiere vs release date)
            if str_year.isdigit():
                target_y_int = int(str_year)
                for m in movies:
                    m_year = (m.get("releaseDate") or "").split("-")[0]
                    m_title = (m.get("title") or "").lower().strip()
                    if m_title == clean_title and m_year.isdigit():
                        if abs(int(m_year) - target_y_int) <= 1:
                            return "movie", m

                if allow_tv:
                    # 4. Exact title & year +/- 1 (TV shows)
                    for t in tv_shows:
                        t_year = (t.get("firstAirDate") or "").split("-")[0]
                        t_title = (t.get("name") or "").lower().strip()
                        if t_title == clean_title and t_year.isdigit():
                            if abs(int(t_year) - target_y_int) <= 1:
                                return "tv", t

            # If target_year was given, do NOT fall back to arbitrary year match
            return None, None

        # 2. If target_year was NOT specified:
        # 5. Exact title match (movies)
        for m in movies:
            m_title = (m.get("title") or "").lower().strip()
            if m_title == clean_title:
                return "movie", m

        if allow_tv:
            for t in tv_shows:
                t_title = (t.get("name") or "").lower().strip()
                if t_title == clean_title:
                    return "tv", t

        if movies:
            return "movie", movies[0]

        if allow_tv and tv_shows:
            return "tv", tv_shows[0]

        return None, None

    def get_media_details(self, media_type: str, tmdb_id: Union[int, str]) -> dict[str, Any]:
        """
        Fetch full media details for given media type ('movie' or 'tv') and TMDB ID.

        Returns:
            dict containing media metadata on success, or empty dict {} on error.
        """
        if not self.is_configured:
            logger.warning("Overseerr get_media_details skipped: service is not configured")
            return {}

        if media_type not in ("movie", "tv"):
            logger.warning("Invalid media_type '%s' requested from Overseerr", media_type)
            return {}

        endpoint = f"{self.url}/api/v1/{media_type}/{tmdb_id}"
        try:
            resp = requests.get(endpoint, headers=self._headers, timeout=self.timeout)
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, dict) else {}

            logger.error(
                "Overseerr get_media_details failed for %s/%s with HTTP %d: %s",
                media_type,
                tmdb_id,
                resp.status_code,
                resp.text[:200],
            )
            return {}

        except requests.RequestException as exc:
            logger.error("Overseerr get_media_details network error for %s/%s: %s", media_type, tmdb_id, exc)
            return {}
        except Exception as exc:
            logger.error("Unexpected error in Overseerr get_media_details for %s/%s: %s", media_type, tmdb_id, exc)
            return {}

    def request_media(
        self,
        media_type: str,
        tmdb_id: Union[int, str],
        seasons: Optional[Union[list[int], str]] = None,
        is_4k: bool = False,
    ) -> bool:
        """
        Submit a media request to Overseerr.

        Args:
            media_type: 'movie' or 'tv'
            tmdb_id: TMDB ID of the item
            seasons: list of season integers (e.g. [1]) or 'all' for TV shows
            is_4k: boolean flag requesting 4K quality profile

        Returns:
            True if request accepted (HTTP 200 or 201), False otherwise.
        """
        if not self.is_configured:
            logger.warning("Overseerr request_media skipped: service is not configured")
            return False

        try:
            numeric_id = int(tmdb_id)
        except (ValueError, TypeError):
            logger.error("Overseerr request_media failed: invalid tmdb_id '%s'", tmdb_id)
            return False

        payload: dict[str, Any] = {
            "mediaType": media_type,
            "mediaId": numeric_id,
            "is4k": bool(is_4k),
        }
        if media_type == "tv" and seasons is not None:
            payload["seasons"] = seasons

        endpoint = f"{self.url}/api/v1/request"
        try:
            resp = requests.post(
                endpoint,
                headers=self._headers,
                json=payload,
                timeout=self.timeout,
            )
            if resp.status_code in (200, 201):
                logger.info("Successfully requested %s (TMDB %d) via Overseerr", media_type, numeric_id)
                return True

            logger.error(
                "Overseerr request_media failed for %s (TMDB %d) with HTTP %d: %s",
                media_type,
                numeric_id,
                resp.status_code,
                resp.text[:200],
            )
            return False

        except requests.RequestException as exc:
            logger.error("Overseerr request_media network error for TMDB %d: %s", numeric_id, exc)
            return False
        except Exception as exc:
            logger.error("Unexpected error in Overseerr request_media for TMDB %d: %s", numeric_id, exc)
            return False

    # ------------------------------------------------------------------
    # Cleanup after a movie has been deleted elsewhere
    # ------------------------------------------------------------------

    def _call(self, method: str, path: str, **kwargs: Any) -> Optional[requests.Response]:
        """One API call; None on a network error so callers can report it."""
        try:
            return requests.request(method, f"{self.url}/api/v1{path}", headers=self._headers, timeout=self.timeout, **kwargs)
        except requests.RequestException as exc:
            logger.error("Overseerr %s %s failed: %s", method, path, exc)
            return None

    def request_ids(self, tmdb_id: int) -> Optional[list[int]]:
        """Ids of the requests held for a movie. Empty if none or unknown, None on an error."""
        resp = self._call("GET", f"/movie/{int(tmdb_id)}")
        if resp is None or resp.status_code not in (200, 404):
            return None
        if resp.status_code == 404:
            return []
        info = resp.json().get("mediaInfo") or {}
        return [r["id"] for r in info.get("requests") or [] if "id" in r]

    def delete_request(self, request_id: int) -> bool:
        """Delete a request. A request that is already gone counts as success."""
        resp = self._call("DELETE", f"/request/{int(request_id)}")
        return resp is not None and resp.status_code in (200, 204, 404)

    def watchlist_tmdb_ids(self, max_pages: int = 50) -> Optional[set[int]]:
        """TMDB ids of movies on the API-key user's Plex watchlist, or None on an error."""
        found: set[int] = set()
        page, total = 1, 1
        while page <= total and page <= max_pages:
            resp = self._call("GET", "/discover/watchlist", params={"page": page})
            if resp is None or resp.status_code != 200:
                return None
            data = resp.json()
            total = data.get("totalPages", 1)
            found |= {i["tmdbId"] for i in data.get("results", []) if i.get("mediaType") == "movie" and i.get("tmdbId")}
            page += 1
        return found

    def remove_from_watchlist(self, tmdb_id: int) -> bool:
        """Remove a movie from the watchlist. Already gone counts as success."""
        resp = self._call("DELETE", f"/watchlist/{int(tmdb_id)}", params={"mediaType": "movie"})
        return resp is not None and resp.status_code in (200, 204, 404)

    def forget_movies(self, titles: dict[int, str]) -> list[str]:
        """
        Once movies are deleted elsewhere, delete their Overseerr requests and take them off the
        watchlist so they cannot be re-requested. `titles` maps TMDB id to title.
        Returns a list of problems (empty when everything was cleared and verified).
        """
        problems: list[str] = []
        on_watchlist = self.watchlist_tmdb_ids()
        if on_watchlist is None:
            problems.append("could not read the watchlist")
            on_watchlist = set()

        for tmdb_id, title in titles.items():
            ids = self.request_ids(tmdb_id)
            if ids is None:
                problems.append(f"{title}: could not read its requests")
            for request_id in ids or []:
                if not self.delete_request(request_id):
                    problems.append(f"{title}: request {request_id} not deleted")
            if tmdb_id in on_watchlist and not self.remove_from_watchlist(tmdb_id):
                problems.append(f"{title}: not removed from the watchlist")

        removed = [t for t in titles if t in on_watchlist]
        if removed:
            after = self.watchlist_tmdb_ids()
            for tmdb_id in removed:
                if after is not None and tmdb_id in after:
                    problems.append(f"{titles[tmdb_id]}: still on the watchlist after removal")
        return problems
