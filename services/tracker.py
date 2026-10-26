"""
services/tracker.py
Collection tracker service for Letterboxd lists, Plex synchronization,
and Overseerr availability webhook reconciliation.
"""

from __future__ import annotations

import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from core.plex import MovieMeta, PlexClient
from core.storage import load_tracked_collections, save_tracked_collections
from services.overseerr import OverseerrService

logger = logging.getLogger(__name__)


def _format_preview_list(items: List[str], max_items: int = 5) -> str:
    """Format movie list for WhatsApp message preview."""
    if not items:
        return "None"
    if len(items) <= max_items:
        return ", ".join(items)
    return f"{', '.join(items[:max_items])} (+{len(items) - max_items} more)"


def _protect(client: PlexClient, keys: List[int], collection: str) -> None:
    """Tag movies Keep Forever so the cleaner skips them, and warn when a tag did not take."""
    labelled = client.add_label(keys, "Keep Forever")
    if isinstance(labelled, int) and labelled < len(keys):
        logger.warning(
            "Only %d of %d movies in '%s' were tagged Keep Forever; the cleaner may flag the rest",
            labelled, len(keys), collection,
        )


def _prune_finished(tracked: Dict[str, Any]) -> bool:
    """Drop collections with nothing left to wait for. Returns True if any were dropped."""
    done = [
        title for title, data in tracked.items()
        if all(item.get("status") == "available" for item in data.get("items", {}).values())
    ]
    for title in done:
        del tracked[title]
    return bool(done)


class CollectionTracker:
    """
    Tracks list items that are still waiting to download so they can join their Plex
    collection when they arrive. A collection is forgotten once everything has arrived;
    its movies stay protected by their Keep Forever tag.
    """

    def __init__(
        self,
        plex: Optional[PlexClient] = None,
        overseerr: Optional[OverseerrService] = None,
        storage_path: Optional[Union[Path, str]] = None,
    ) -> None:
        self.plex = plex
        self.overseerr = overseerr
        self.storage_path = Path(storage_path) if storage_path else None

    @classmethod
    def from_config(
        cls,
        cfg: Optional[Dict[str, Any]] = None,
        storage_path: Optional[Union[Path, str]] = None,
    ) -> CollectionTracker:
        """Instantiate CollectionTracker from application config dictionary or default config."""
        if cfg is None:
            from core.config import load_config
            cfg = load_config()

        plex = PlexClient.from_config(cfg) if cfg.get("plex") else None
        overseerr = OverseerrService.from_config(cfg) if cfg.get("overseerr") else None
        return cls(plex=plex, overseerr=overseerr, storage_path=storage_path)

    # ------------------------------------------------------------------
    # Storage helpers
    # ------------------------------------------------------------------

    def _load_state(self) -> Dict[str, Any]:
        """Load tracked collections atomically."""
        return load_tracked_collections(self.storage_path)

    def _save_state(self, data: Dict[str, Any]) -> None:
        """Save tracked collections atomically."""
        save_tracked_collections(data, self.storage_path)

    def _get_plex(self, plex: Optional[PlexClient] = None) -> Optional[PlexClient]:
        """Resolve PlexClient: argument > instance attribute > from_config fallback."""
        if plex is not None:
            return plex
        if self.plex is not None:
            return self.plex
        try:
            from core.config import load_config
            cfg = load_config()
            self.plex = PlexClient.from_config(cfg)
            return self.plex
        except Exception as e:
            logger.debug("Could not resolve PlexClient from config: %s", e)
            return None

    def _get_overseerr(self, overseerr: Optional[OverseerrService] = None) -> Optional[OverseerrService]:
        """Resolve OverseerrService: argument > instance attribute > from_config fallback."""
        if overseerr is not None:
            return overseerr
        if self.overseerr is not None:
            return self.overseerr
        try:
            from core.config import load_config
            cfg = load_config()
            self.overseerr = OverseerrService.from_config(cfg)
            return self.overseerr
        except Exception as e:
            logger.debug("Could not resolve OverseerrService from config: %s", e)
            return None

    # ------------------------------------------------------------------
    # Collection reconciliation
    # ------------------------------------------------------------------

    def sync_tracked_collections(self, plex: Optional[PlexClient] = None) -> None:
        """
        Reconciles all pending items across tracked collections against Plex.
        Adds newly found movies to their collections and marks them as available.
        Performs TMDB ID matching first, then falls back to title matching.
        """
        tracked = self._load_state()
        if not tracked:
            return

        client = self._get_plex(plex)
        if not client:
            logger.warning("No PlexClient available for sync_tracked_collections")
            return

        try:
            movies = client.fetch_movies(force_refresh=True)
        except Exception as e:
            logger.error("Error fetching Plex movies during sync: %s", e)
            return

        movies_by_tmdb: Dict[int, MovieMeta] = {m.tmdb_id: m for m in movies if m.tmdb_id is not None}
        movies_by_title: Dict[str, MovieMeta] = {m.title.lower().strip(): m for m in movies if m.title}

        updated = False
        for coll_title, coll_data in tracked.items():
            items = coll_data.get("items", {})
            keys_to_add: List[int] = []

            for tmdb_str, item in items.items():
                if item.get("status") == "pending":
                    m: Optional[MovieMeta] = None
                    try:
                        t_id = int(tmdb_str)
                        m = movies_by_tmdb.get(t_id)
                    except (ValueError, TypeError):
                        pass

                    if not m and item.get("title"):
                        m = movies_by_title.get(item["title"].lower().strip())

                    if m:
                        item["status"] = "available"
                        item["ratingKey"] = m.ratingKey
                        keys_to_add.append(m.ratingKey)
                        updated = True

            if keys_to_add:
                try:
                    client.add_to_collection(
                        coll_title,
                        keys_to_add,
                        promote=coll_data.get("promote", True),
                    )
                    if coll_data.get("protect", True):
                        _protect(client, keys_to_add, coll_title)
                except Exception as e:
                    logger.error("Error syncing items to collection '%s': %s", coll_title, e)

        if _prune_finished(tracked) or updated:
            self._save_state(tracked)

    # ------------------------------------------------------------------
    # Webhook availability handler
    # ------------------------------------------------------------------

    def handle_media_available(
        self,
        tmdb_id: int,
        title_hint: Optional[str] = None,
        plex: Optional[PlexClient] = None,
        max_retries: int = 3,
        retry_delay: float = 2.0,
    ) -> List[Dict[str, Any]]:
        """
        Checks if a newly available movie belongs to any active tracked collections.
        If matched, adds it to the Plex collection and applies 'Keep Forever' label.

        Returns:
            List of match dictionaries containing collection_title, ready_count, total_count, sender.
        """
        tracked = self._load_state()
        if not tracked:
            return []

        matched: List[Tuple[str, Dict[str, Any]]] = []
        item_key = str(tmdb_id)

        for coll_title, coll_data in tracked.items():
            items = coll_data.get("items", {})
            if item_key in items and items[item_key].get("status") == "pending":
                matched.append((coll_title, coll_data))

        if not matched:
            return []

        client = self._get_plex(plex)
        plex_movie: Optional[MovieMeta] = None

        if client:
            for attempt in range(max_retries):
                try:
                    movies = client.fetch_movies(force_refresh=True)
                    for m in movies:
                        if m.tmdb_id == tmdb_id:
                            plex_movie = m
                            break
                        if title_hint and m.title and m.title.lower().strip() == title_hint.lower().strip():
                            plex_movie = m
                            break
                    if plex_movie:
                        break
                except Exception as e:
                    logger.debug("Error querying Plex for new media (attempt %d): %s", attempt + 1, e)

                if attempt < max_retries - 1 and retry_delay > 0:
                    time.sleep(retry_delay)

        results: List[Dict[str, Any]] = []
        for coll_title, coll_data in matched:
            item = coll_data["items"][item_key]
            item["status"] = "available"
            if plex_movie and client:
                item["ratingKey"] = plex_movie.ratingKey
                try:
                    client.add_to_collection(
                        coll_title,
                        [plex_movie.ratingKey],
                        promote=coll_data.get("promote", True),
                    )
                    if coll_data.get("protect", True):
                        _protect(client, [plex_movie.ratingKey], coll_title)
                except Exception as e:
                    logger.error("Error adding %d to collection '%s': %s", tmdb_id, coll_title, e)

            ready_count = sum(1 for it in coll_data["items"].values() if it.get("status") == "available")
            total_count = len(coll_data["items"])
            results.append({
                "collection_title": coll_title,
                "ready_count": ready_count,
                "total_count": total_count,
                "sender": coll_data.get("sender"),
            })

        _prune_finished(tracked)
        self._save_state(tracked)
        return results

    # ------------------------------------------------------------------
    # Letterboxd list processing
    # ------------------------------------------------------------------

    def process_letterboxd_collection(
        self,
        sender: str,
        list_title: str,
        titles: List[Union[str, Dict[str, Any]]],
        create_collection: bool = True,
        notifier: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """
        Process items scraped from a Letterboxd list:
        - Matches against Plex library
        - Requests missing items via Overseerr
        - If create_collection=True, pins collection in Plex, marks Keep Forever, and saves tracked state.
        - Optionally sends WhatsApp notification via notifier.
        """
        client = self._get_plex() if create_collection else None
        os_service = self._get_overseerr()

        plex_by_tmdb: Dict[int, MovieMeta] = {}
        plex_by_title: Dict[str, MovieMeta] = {}
        if client:
            try:
                plex_movies = client.fetch_movies()
                for m in plex_movies:
                    if m.tmdb_id:
                        plex_by_tmdb[m.tmdb_id] = m
                    if m.title:
                        plex_by_title[m.title.lower().strip()] = m
            except Exception as e:
                logger.error("Error fetching Plex movies: %s", e)

        requested_titles: List[str] = []
        skipped_titles: List[str] = []
        failed_titles: List[str] = []
        already_in_plex_keys: List[int] = []
        already_in_plex_titles: List[str] = []
        tracked_items: Dict[str, Any] = {}

        for item in titles:
            if isinstance(item, dict):
                raw_title = item.get("title", "")
                target_year = str(item.get("year")) if item.get("year") else None
            else:
                match = re.match(r"^(.*?)\s*\((\d{4})\)$", str(item))
                if match:
                    raw_title = match.group(1).strip()
                    target_year = match.group(2)
                else:
                    raw_title = str(item).strip()
                    target_year = None

            if not os_service:
                failed_titles.append(raw_title)
                continue

            media_list = os_service.search(raw_title)
            if not media_list:
                failed_titles.append(raw_title)
                continue

            media_type, media_item = os_service.find_best_match(
                raw_title,
                target_year=target_year,
                media_list=media_list,
                allow_tv=False,
            )
            if not media_item:
                failed_titles.append(raw_title)
                continue

            tmdb_id = media_item.get("id")
            movie_title = media_item.get("title") if media_type == "movie" else media_item.get("name") or raw_title
            year = (media_item.get("releaseDate") if media_type == "movie" else media_item.get("firstAirDate") or "").split("-")[0]

            # Check if movie already exists in Plex
            plex_movie: Optional[MovieMeta] = None
            if client and media_type == "movie":
                if tmdb_id in plex_by_tmdb:
                    plex_movie = plex_by_tmdb[tmdb_id]
                elif raw_title.lower().strip() in plex_by_title:
                    plex_movie = plex_by_title[raw_title.lower().strip()]

            if plex_movie:
                already_in_plex_keys.append(plex_movie.ratingKey)
                already_in_plex_titles.append(movie_title)
                if create_collection:
                    tracked_items[str(tmdb_id)] = {
                        "title": movie_title,
                        "year": year,
                        "mediaType": media_type,
                        "status": "available",
                        "ratingKey": plex_movie.ratingKey,
                    }
            else:
                success = False
                if media_type == "tv":
                    success = os_service.request_media("tv", tmdb_id, seasons=[1])
                else:
                    success = os_service.request_media("movie", tmdb_id)

                if success:
                    requested_titles.append(movie_title)
                else:
                    skipped_titles.append(movie_title)

                if create_collection:
                    tracked_items[str(tmdb_id)] = {
                        "title": movie_title,
                        "year": year,
                        "mediaType": media_type,
                        "status": "pending",
                        "ratingKey": None,
                    }

        if create_collection and client:
            if already_in_plex_keys:
                try:
                    client.add_to_collection(list_title, already_in_plex_keys, promote=True)
                    _protect(client, already_in_plex_keys, list_title)
                except Exception as e:
                    logger.error("Error adding existing movies to Plex collection '%s': %s", list_title, e)

            # Only collections with films still on their way need tracking.
            if any(item["status"] == "pending" for item in tracked_items.values()):
                tracked = self._load_state()
                tracked[list_title] = {
                    "title": list_title,
                    "sender": sender,
                    "created_at": datetime.now(timezone.utc).isoformat(),
                    "protect": True,
                    "promote": True,
                    "items": tracked_items,
                }
                self._save_state(tracked)

        # Build notification message
        msg = f"✅ *{list_title}* processed!\n\n"
        if create_collection:
            msg += f"📁 *Plex Collection*: Created with {len(already_in_plex_keys)} film(s) already in Plex (Promoted to Home).\n"
        msg += f"📥 *Requested ({len(requested_titles)})*: {_format_preview_list(requested_titles)}\n"
        if skipped_titles:
            msg += f"⏭️ *Already Requested/Skipped ({len(skipped_titles)})*: {_format_preview_list(skipped_titles)}\n"
        if failed_titles:
            msg += f"❌ *Not Found on TMDB ({len(failed_titles)})*: {_format_preview_list(failed_titles)}\n"
        if create_collection and requested_titles:
            msg += f"\n🍿 I'll automatically add the remaining {len(requested_titles)} films to the collection as they download!"

        if notifier and hasattr(notifier, "send_message"):
            try:
                notifier.send_message(sender, msg)
            except Exception as e:
                logger.error("Error sending WhatsApp notification: %s", e)

        return {
            "list_title": list_title,
            "already_in_plex_keys": already_in_plex_keys,
            "already_in_plex_titles": already_in_plex_titles,
            "requested": requested_titles,
            "skipped": skipped_titles,
            "failed": failed_titles,
            "message": msg,
        }


# ==============================================================================
# Module-level convenience functions
# ==============================================================================

def sync_tracked_collections(
    plex: Optional[PlexClient] = None,
    storage_path: Optional[Union[Path, str]] = None,
) -> None:
    """Convenience module function for sync_tracked_collections."""
    tracker = CollectionTracker(plex=plex, storage_path=storage_path)
    tracker.sync_tracked_collections(plex=plex)


def handle_media_available(
    tmdb_id: int,
    title_hint: Optional[str] = None,
    plex: Optional[PlexClient] = None,
    storage_path: Optional[Union[Path, str]] = None,
) -> List[Dict[str, Any]]:
    """Convenience module function for handle_media_available."""
    tracker = CollectionTracker(plex=plex, storage_path=storage_path)
    return tracker.handle_media_available(tmdb_id=tmdb_id, title_hint=title_hint, plex=plex)


def process_letterboxd_collection(
    sender: str,
    list_title: str,
    titles: List[Union[str, Dict[str, Any]]],
    create_collection: bool = True,
    plex: Optional[PlexClient] = None,
    overseerr: Optional[OverseerrService] = None,
    storage_path: Optional[Union[Path, str]] = None,
    notifier: Optional[Any] = None,
) -> Dict[str, Any]:
    """Convenience module function for process_letterboxd_collection."""
    tracker = CollectionTracker(plex=plex, overseerr=overseerr, storage_path=storage_path)
    return tracker.process_letterboxd_collection(
        sender=sender,
        list_title=list_title,
        titles=titles,
        create_collection=create_collection,
        notifier=notifier,
    )
