"""
services package
External API adapters for Overseerr, Radarr, Letterboxd, and Collection Tracker.
"""

from services.overseerr import OverseerrService
from services.radarr import RadarrService
from services.letterboxd import (
    build_list_prompt,
    fetch_letterboxd_html,
    scrape_letterboxd_list,
    extract_letterboxd_film_metadata,
)
from services.tracker import (
    CollectionTracker,
    sync_tracked_collections,
    handle_media_available,
    process_letterboxd_collection,
)

__all__ = [
    "OverseerrService",
    "RadarrService",
    "build_list_prompt",
    "fetch_letterboxd_html",
    "scrape_letterboxd_list",
    "extract_letterboxd_film_metadata",
    "CollectionTracker",
    "sync_tracked_collections",
    "handle_media_available",
    "process_letterboxd_collection",
]
