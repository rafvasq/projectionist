"""
Plex client — connects to a local Plex server, fetches movie and TV metadata,
and writes collections back.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from plexapi.server import PlexServer
from plexapi.exceptions import NotFound, PlexApiException

logger = logging.getLogger(__name__)


@dataclass
class MovieMeta:
    """Flat, serialisable snapshot of a Plex movie's metadata."""

    ratingKey: int
    title: str
    year: Optional[int]
    summary: str
    rating: Optional[float]          # critic / Plex rating (0–10)
    audience_rating: Optional[float] # audience rating (0–10)
    added_at: Optional[datetime]
    last_viewed_at: Optional[datetime]
    genres: list[str] = field(default_factory=list)
    duration_ms: Optional[int] = None
    collections: list[str] = field(default_factory=list)
    labels: list[str] = field(default_factory=list)
    tmdb_id: Optional[int] = None

    @property
    def watched(self) -> bool:
        return self.last_viewed_at is not None

    @property
    def rating_pct(self) -> Optional[float]:
        return self.rating * 10 if self.rating is not None else None

    @property
    def audience_rating_pct(self) -> Optional[float]:
        return self.audience_rating * 10 if self.audience_rating is not None else None


@dataclass
class ShowMeta:
    """Flat, serialisable snapshot of a Plex TV show's metadata."""

    ratingKey: int
    title: str
    year: Optional[int]
    summary: str
    audience_rating: Optional[float]  # 0–10; critic rating is rarely populated for TV
    added_at: Optional[datetime]
    last_viewed_at: Optional[datetime]
    viewed_episode_count: int         # episodes watched so far
    total_episode_count: int          # total episodes in library
    genres: list[str] = field(default_factory=list)

    @property
    def never_started(self) -> bool:
        return self.viewed_episode_count == 0

    @property
    def in_progress(self) -> bool:
        return 0 < self.viewed_episode_count < self.total_episode_count

    @property
    def audience_rating_pct(self) -> Optional[float]:
        return self.audience_rating * 10 if self.audience_rating is not None else None


class PlexClient:
    """Thin wrapper around python-plexapi for Projectionist."""

    def __init__(self, url: str, token: str, library: str = "Movies") -> None:
        self._url = url
        self._token = token
        self._library_name = library
        self._server: Optional[PlexServer] = None
        self._library_sections_cache: dict = {}
        self._library_items_cache: dict = {}
        self._items_lookup_by_key: dict = {}

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Establish (or re-establish) a connection to the Plex server."""
        logger.info("Connecting to Plex at %s …", self._url)
        self._server = PlexServer(self._url, self._token)
        logger.info("Connected — server name: %s", self._server.friendlyName)

    @property
    def server(self) -> PlexServer:
        if self._server is None:
            self.connect()
        return self._server  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Metadata fetching
    # ------------------------------------------------------------------

    def _get_library_section(self, name: str):
        """Retrieve a library section by name, caching it to avoid redundant lookups."""
        if name not in self._library_sections_cache:
            try:
                self._library_sections_cache[name] = self.server.library.section(name)
            except NotFound:
                raise ValueError(
                    f"Library '{name}' not found on this Plex server."
                )
        return self._library_sections_cache[name]

    def _get_library_items(self, name: str):
        """Retrieve all items in a library section, caching them and pre-indexing by ratingKey."""
        if name not in self._library_items_cache:
            library = self._get_library_section(name)
            items = library.all()
            self._library_items_cache[name] = items
            self._items_lookup_by_key[name] = {m.ratingKey: m for m in items}
        return self._library_items_cache[name]

    def fetch_movies(self) -> list[MovieMeta]:
        """Return a list of MovieMeta for every item in the configured library."""
        movies = self._get_library_items(self._library_name)
        logger.info("Fetched %d movies from '%s'", len(movies), self._library_name)
        return [self._movie_to_meta(m) for m in movies]

    def fetch_shows(self, library_name: str) -> list[ShowMeta]:
        """Return a list of ShowMeta for every show in the given TV library."""
        shows = self._get_library_items(library_name)
        logger.info("Fetched %d shows from '%s'", len(shows), library_name)
        return [self._show_to_meta(s) for s in shows]

    @staticmethod
    def _movie_to_meta(movie) -> MovieMeta:
        collections = [c.tag for c in getattr(movie, "collections", [])]
        labels = [l.tag for l in getattr(movie, "labels", [])]
        
        tmdb_id = None
        for guid in getattr(movie, "guids", []):
            if guid.id.startswith("tmdb://"):
                try: 
                    tmdb_id = int(guid.id.split("tmdb://")[1])
                except: 
                    pass
                    
        return MovieMeta(
            ratingKey=movie.ratingKey,
            title=movie.title,
            year=getattr(movie, "year", None),
            summary=movie.summary or "",
            rating=getattr(movie, "rating", None),
            audience_rating=getattr(movie, "audienceRating", None),
            added_at=getattr(movie, "addedAt", None),
            last_viewed_at=getattr(movie, "lastViewedAt", None),
            genres=[g.tag for g in getattr(movie, "genres", [])],
            duration_ms=getattr(movie, "duration", None),
            collections=collections,
            labels=labels,
            tmdb_id=tmdb_id
        )

    @staticmethod
    def _show_to_meta(show) -> ShowMeta:
        return ShowMeta(
            ratingKey=show.ratingKey,
            title=show.title,
            year=getattr(show, "year", None),
            summary=show.summary or "",
            audience_rating=getattr(show, "audienceRating", None),
            added_at=getattr(show, "addedAt", None),
            last_viewed_at=getattr(show, "lastViewedAt", None),
            viewed_episode_count=getattr(show, "viewedLeafCount", 0) or 0,
            total_episode_count=getattr(show, "leafCount", 0) or 0,
            genres=[g.tag for g in getattr(show, "genres", [])],
        )

    # ------------------------------------------------------------------
    # Collection management
    # ------------------------------------------------------------------

    def upsert_collection(self, title: str, keys: list[int], library_name: Optional[str] = None) -> None:
        """
        Create or fully replace a library collection with the given title.
        Collections can be pinned to the Plex home screen as hub rows.

        Args:
            title:        Display name of the collection on Plex.
            keys:         List of ratingKey integers.
            library_name: Override library (defaults to self._library_name).
        """
        if not keys:
            logger.warning("upsert_collection called with empty list for '%s' — skipping", title)
            return

        lib_name = library_name or self._library_name
        library = self.server.library.section(lib_name)
        items = self._fetch_items_by_keys(library, keys)

        if not items:
            logger.warning("No matching Plex items found for collection '%s' — skipping", title)
            return

        try:
            library.collection(title).delete()
            logger.debug("Deleted existing collection '%s'", title)
        except NotFound:
            pass
        except PlexApiException as exc:
            logger.warning("Could not delete collection '%s': %s", title, exc)

        random.shuffle(items)
        collection = library.createCollection(title, items=items)
        collection.sortUpdate(sort="custom")
        logger.info("Collection '%s' created with %d items (randomised)", collection.title, len(items))

        try:
            collection.visibility().promoteRecommended()
            logger.debug("Collection '%s' promoted to home screen", title)
        except Exception as exc:
            logger.warning("Could not promote collection '%s': %s", title, exc)

    def _fetch_items_by_keys(self, library, keys: list[int]):
        """Bulk-fetch Plex media objects by their ratingKeys using cache dictionary."""
        lib_name = library.title
        self._get_library_items(lib_name)
        lookup = self._items_lookup_by_key[lib_name]
        items = [lookup[k] for k in keys if k in lookup]
        order = {k: i for i, k in enumerate(keys)}
        items.sort(key=lambda m: order.get(m.ratingKey, len(keys)))
        return items

    def delete_collection(self, title: str, library_name: Optional[str] = None) -> bool:
        """Delete a collection by name. Returns True if deleted, False if not found."""
        lib_name = library_name or self._library_name
        library = self.server.library.section(lib_name)
        try:
            library.collection(title).delete()
            logger.info("Deleted collection '%s'", title)
            return True
        except NotFound:
            return False


# ------------------------------------------------------------------
# Factory from config dict
# ------------------------------------------------------------------

def from_config(cfg: dict) -> PlexClient:
    """Construct a PlexClient from the 'plex' section of config.yaml."""
    plex_cfg = cfg.get("plex", {})
    return PlexClient(
        url=plex_cfg["url"],
        token=plex_cfg["token"],
        library=plex_cfg.get("library", "Movies"),
    )
