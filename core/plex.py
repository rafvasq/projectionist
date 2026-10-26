"""
core/plex.py
Plex client — connects to a Plex server, fetches movie metadata,
and manages collections and labels without TV show dead code.
"""

from __future__ import annotations

import logging
import random
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from plexapi.exceptions import NotFound, PlexApiException
from plexapi.server import PlexServer

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


class PlexClient:
    """Thin wrapper around python-plexapi for Projectionist movie management."""

    def __init__(self, url: str, token: str, library: str = "Movies") -> None:
        self._url = url
        self._token = token
        self._library_name = library
        self._server: Optional[PlexServer] = None
        self._library_sections_cache: dict[str, Any] = {}
        self._library_items_cache: dict[str, list[Any]] = {}
        self._items_lookup_by_key: dict[str, dict[int, Any]] = {}

    @classmethod
    def from_config(cls, cfg: dict[str, Any]) -> PlexClient:
        """Construct PlexClient from configuration dictionary."""
        plex_cfg = cfg.get("plex", {}) if isinstance(cfg, dict) else {}
        return cls(
            url=plex_cfg.get("url", ""),
            token=plex_cfg.get("token", ""),
            library=plex_cfg.get("library", "Movies"),
        )

    # ------------------------------------------------------------------
    # Connection
    # ------------------------------------------------------------------

    def connect(self) -> None:
        """Establish connection to the Plex server."""
        logger.info("Connecting to Plex at %s …", self._url)
        self._server = PlexServer(self._url, self._token)
        logger.info("Connected — server name: %s", self._server.friendlyName)

    @property
    def server(self) -> PlexServer:
        if self._server is None:
            self.connect()
        return self._server  # type: ignore[return-value]

    # ------------------------------------------------------------------
    # Metadata fetching & caching
    # ------------------------------------------------------------------

    def _get_library_section(self, name: str) -> Any:
        """Retrieve library section by name with caching."""
        if name not in self._library_sections_cache:
            try:
                self._library_sections_cache[name] = self.server.library.section(name)
            except NotFound:
                raise ValueError(f"Library '{name}' not found on this Plex server.")
        return self._library_sections_cache[name]

    def _get_library_items(self, name: str, force_refresh: bool = False) -> list[Any]:
        """Retrieve all items in library section, caching and indexing by ratingKey."""
        if force_refresh or name not in self._library_items_cache:
            library = self._get_library_section(name)
            items = library.all()
            self._library_items_cache[name] = items
            self._items_lookup_by_key[name] = {m.ratingKey: m for m in items}
        return self._library_items_cache[name]

    def fetch_movies(self, force_refresh: bool = False) -> list[MovieMeta]:
        """Return list of MovieMeta for all movies in configured library."""
        movies = self._get_library_items(self._library_name, force_refresh=force_refresh)
        logger.info("Fetched %d movies from '%s'", len(movies), self._library_name)
        return [self._movie_to_meta(m) for m in movies]

    @staticmethod
    def _movie_to_meta(movie: Any) -> MovieMeta:
        collections = [c.tag for c in getattr(movie, "collections", [])]
        labels = [l.tag for l in getattr(movie, "labels", [])]

        tmdb_id: Optional[int] = None
        for guid in getattr(movie, "guids", []):
            guid_id = getattr(guid, "id", "")
            if isinstance(guid_id, str) and guid_id.startswith("tmdb://"):
                try:
                    tmdb_id = int(guid_id.split("tmdb://")[1])
                    break
                except (ValueError, IndexError, AttributeError):
                    pass

        return MovieMeta(
            ratingKey=movie.ratingKey,
            title=movie.title,
            year=getattr(movie, "year", None),
            summary=getattr(movie, "summary", "") or "",
            rating=getattr(movie, "rating", None),
            audience_rating=getattr(movie, "audienceRating", None),
            added_at=getattr(movie, "addedAt", None),
            last_viewed_at=getattr(movie, "lastViewedAt", None),
            genres=[g.tag for g in getattr(movie, "genres", [])],
            duration_ms=getattr(movie, "duration", None),
            collections=collections,
            labels=labels,
            tmdb_id=tmdb_id,
        )

    # ------------------------------------------------------------------
    # Collection management & promotion
    # ------------------------------------------------------------------

    @classmethod
    def _promote_collection(cls, collection: Any, title: Optional[str] = None) -> None:
        """Promote collection to Plex home screen and library recommendations."""
        title_str = title or getattr(collection, "title", "Unknown")
        try:
            vis = collection.visibility()
            try:
                vis.promoteHome()
            except Exception:
                pass
            try:
                vis.promoteRecommended()
            except Exception:
                pass
            logger.debug("Collection '%s' promoted to home screen and recommendations", title_str)
        except Exception as exc:
            logger.warning("Could not promote collection '%s': %s", title_str, exc)

    def upsert_collection(self, title: str, keys: list[int], library_name: Optional[str] = None) -> None:
        """Create or replace a library collection with randomised order and home pin."""
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

        self._promote_collection(collection, title)

    def add_to_collection(
        self,
        title: str,
        keys: list[int],
        library_name: Optional[str] = None,
        promote: bool = True,
    ) -> None:
        """Add items to existing collection or create it if absent. Preserves order."""
        if not keys:
            logger.warning("add_to_collection called with empty list for '%s' — skipping", title)
            return

        lib_name = library_name or self._library_name
        library = self.server.library.section(lib_name)
        items = self._fetch_items_by_keys(library, keys)

        if not items:
            logger.warning("No matching Plex items found for collection '%s' — skipping", title)
            return

        try:
            collection = library.collection(title)
            collection.addItems(items)
            logger.info("Added %d items to existing collection '%s'", len(items), title)
        except NotFound:
            collection = library.createCollection(title, items=items)
            collection.sortUpdate(sort="custom")
            logger.info("Collection '%s' created with %d items", title, len(items))

        if promote:
            self._promote_collection(collection, title)

    def add_label(self, keys: list[int], label: str = "Keep Forever", library_name: Optional[str] = None) -> int:
        """Add a label to items in Plex. Returns the number of items successfully labelled."""
        if not keys:
            return 0

        lib_name = library_name or self._library_name
        library = self.server.library.section(lib_name)
        items = self._fetch_items_by_keys(library, keys)

        changed = 0
        for item in items:
            try:
                item.addLabel(label)
                changed += 1
                logger.debug("Added label '%s' to '%s'", label, getattr(item, "title", item))
            except Exception as exc:
                logger.warning("Could not add label '%s' to item %s: %s", label, item, exc)
        return changed

    def remove_label(self, keys: list[int], label: str = "Keep Forever", library_name: Optional[str] = None) -> int:
        """Remove a label from items in Plex. Returns the number of items successfully updated."""
        if not keys:
            return 0

        lib_name = library_name or self._library_name
        library = self.server.library.section(lib_name)
        items = self._fetch_items_by_keys(library, keys)

        changed = 0
        for item in items:
            try:
                item.removeLabel(label)
                changed += 1
                logger.debug("Removed label '%s' from '%s'", label, getattr(item, "title", item))
            except Exception as exc:
                logger.warning("Could not remove label '%s' from item %s: %s", label, item, exc)
        return changed

    @staticmethod
    def _watchlist_by_tmdb(account: Any) -> dict[int, Any]:
        """Movies on the account's Plex watchlist, keyed by TMDB id."""
        found: dict[int, Any] = {}
        for item in account.watchlist():
            if getattr(item, "type", None) != "movie":
                continue
            for guid in getattr(item, "guids", None) or []:
                if guid.id.startswith("tmdb://"):
                    found[int(guid.id.split("//", 1)[1])] = item
        return found

    def remove_from_watchlist(self, titles: dict[int, str]) -> list[str]:
        """
        Take movies (TMDB id -> title) off the owner's Plex watchlist, then check each one is
        really gone. Movies not on the watchlist are skipped. Returns problems; empty when verified.
        Finding the movies reads the whole watchlist, which takes about a minute for 175 movies.
        """
        try:
            account = self.server.myPlexAccount()
            on_list = self._watchlist_by_tmdb(account)
        except Exception as exc:
            logger.error("Could not read the Plex watchlist: %s", exc)
            return [f"could not read the Plex watchlist: {exc}"]

        problems: list[str] = []
        removed: dict[int, str] = {}
        for tmdb_id, title in titles.items():
            if tmdb_id not in on_list:
                continue
            try:
                account.removeFromWatchlist(on_list[tmdb_id])
                removed[tmdb_id] = title
            except Exception as exc:
                problems.append(f"{title}: not removed from the watchlist ({exc})")

        # Re-check only the movies just removed: reading ids for the whole list is slow (a call per item).
        try:
            still_there = [title for t, title in removed.items() if account.onWatchlist(on_list[t])]
        except Exception as exc:
            return problems + [f"could not re-check the watchlist: {exc}"]
        return problems + [f"{title}: still on the watchlist after removal" for title in still_there]

    def _fetch_items_by_keys(self, library: Any, keys: list[int]) -> list[Any]:
        """Bulk-fetch Plex media objects by ratingKey using cache dictionary."""
        lib_name = library.title
        self._get_library_items(lib_name)
        lookup = self._items_lookup_by_key[lib_name]
        missing = [k for k in keys if k not in lookup]
        if missing:
            self._get_library_items(lib_name, force_refresh=True)
            lookup = self._items_lookup_by_key[lib_name]
        items = [lookup[k] for k in keys if k in lookup]
        order = {k: i for i, k in enumerate(keys)}
        items.sort(key=lambda m: order.get(m.ratingKey, len(keys)))
        return items

    def list_collections(self, library_name: Optional[str] = None) -> list[str]:
        """Names of the collections in the movie library."""
        library = self.server.library.section(library_name or self._library_name)
        return [c.title for c in library.collections()]

    def collection_keys(self, title: str, library_name: Optional[str] = None) -> list[int]:
        """ratingKeys of the movies in a collection. Empty if it doesn't exist."""
        library = self.server.library.section(library_name or self._library_name)
        try:
            return [m.ratingKey for m in library.collection(title).items()]
        except NotFound:
            return []

    def delete_collection(self, title: str, library_name: Optional[str] = None) -> bool:
        """Delete collection by name. Returns True if deleted, False if not found."""
        lib_name = library_name or self._library_name
        library = self.server.library.section(lib_name)
        try:
            library.collection(title).delete()
            logger.info("Deleted collection '%s'", title)
            return True
        except NotFound:
            return False


def from_config(cfg: dict[str, Any]) -> PlexClient:
    """Construct a PlexClient from the 'plex' section of config."""
    return PlexClient.from_config(cfg)
