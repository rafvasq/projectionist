"""
jobs/curator.py
AI-curated collection generator, cooldown deduplicator,
and tracked collections synchronizer for Projectionist.
(The Leaving Soon row is maintained by the cleaner from the deletion queue.)

Usage:
    python -m jobs.curator [--config PATH]
"""

from __future__ import annotations

import argparse
import json
import logging
import random
from pathlib import Path
from typing import Any, Optional, Union

from core.config import load_config
from core.plex import MovieMeta, PlexClient
from core.ai import setup_ai_env, curate_row, ai_session
from core.storage import (
    load_cooldown,
    save_cooldown,
    load_curator_state,
    save_curator_state,
)
from services.tracker import CollectionTracker

logger = logging.getLogger(__name__)

DEFAULT_MAX_RESULTS = 15
DEFAULT_COOLDOWN_WEEKS = 3


def _pick(keys: list[int], seen: set[int], max_results: int) -> list[int]:
    """
    Shuffle candidates, exclude already-used keys, cap at max_results.
    Updates seen in-place so subsequent rows don't repeat the same titles.
    """
    if max_results <= 0:
        return []
    shuffled = list(keys)
    random.shuffle(shuffled)
    fresh = [k for k in shuffled if k not in seen][:max_results]
    seen.update(fresh)
    return fresh


def _build_payload(movies: list[MovieMeta]) -> list[dict[str, Any]]:
    """Convert MovieMeta list to compact dicts for the AI provider."""
    return [
        {
            "ratingKey": m.ratingKey,
            "title": m.title,
            "year": m.year,
            "genres": m.genres,
            "rating": m.rating_pct,
        }
        for m in movies
    ]


def _delete_previous_collections(
    client: PlexClient,
    state_path: Optional[Union[Path, str]] = None,
) -> None:
    """Delete all collections generated in the previous curator run."""
    try:
        records = load_curator_state(state_path)
    except Exception as exc:
        logger.warning("Could not load previous collections state: %s", exc)
        return

    if isinstance(records, list):
        for rec in records:
            if isinstance(rec, dict):
                title = rec.get("title")
                lib_name = rec.get("library")
                if title:
                    try:
                        client.delete_collection(title, library_name=lib_name)
                    except Exception as exc:
                        logger.warning("Could not delete collection '%s': %s", title, exc)


def run(
    config_path: Optional[str] = None,
    cfg: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """
    Main execution pipeline for weekly AI curation and tracked collection sync.
    """
    if cfg is None:
        cfg = load_config(config_path)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
    )

    client = PlexClient.from_config(cfg)
    client.connect()

    # 1. Synchronize tracked collections (Letterboxd / Overseerr pending items)
    try:
        tracker = CollectionTracker(plex=client)
        tracker.sync_tracked_collections(plex=client)
        logger.info("Tracked collections synchronization complete.")
    except Exception as exc:
        logger.error("Error during tracked collections synchronization: %s", exc)

    # 2. Cleanup previous curator collections
    state_path = cfg.get("state_path")
    _delete_previous_collections(client, state_path=state_path)

    # 3. Read configuration parameters
    rows_cfg = cfg.get("rows", {}) if isinstance(cfg, dict) else {}
    count = int(rows_cfg.get("count", 1) or 1)
    max_results = int(rows_cfg.get("max_results", DEFAULT_MAX_RESULTS) or DEFAULT_MAX_RESULTS)
    cooldown_weeks = int(rows_cfg.get("cooldown_weeks", DEFAULT_COOLDOWN_WEEKS) or DEFAULT_COOLDOWN_WEEKS)
    cooldown_path = cfg.get("cooldown_path")

    # 4. Load cooldown history atomically
    cooldown_history, cooldown_keys = load_cooldown(cooldown_weeks, cooldown_path)

    # 5. Fetch library movies and filter unwatched
    all_movies = client.fetch_movies()
    logger.info("Movies library: %d titles", len(all_movies))

    movies = [m for m in all_movies if not m.watched]
    logger.info("Unwatched movies: %d titles", len(movies))

    # 6. Configure AI environment
    ai_model = setup_ai_env(cfg)

    movie_seen: set[int] = set(cooldown_keys)
    exclude_themes: list[str] = []
    collections_written: list[tuple[str, int]] = []
    collections_created_records: list[dict[str, str]] = []
    all_surfaced_keys: list[int] = []

    # 7. Curate AI collections
    for i in range(count):
        available_movies = [m for m in movies if m.ratingKey not in movie_seen]
        if len(available_movies) < 10:
            logger.warning("Not enough eligible movies left (%d) to curate row. Stopping.", len(available_movies))
            break

        payload = _build_payload(available_movies)
        movies_json = json.dumps(payload, ensure_ascii=False)

        exclude_str = ""
        if exclude_themes:
            exclude_str = (
                "\nDo NOT reuse any of these existing collection themes: "
                + ", ".join(exclude_themes) + ".\n"
            )

        try:
            with start_session(ai_model) as session:
                result = curate_row(
                    m=session,
                    movies_json=movies_json,
                    exclude_themes_str=exclude_str,
                    valid_keys={m.ratingKey for m in available_movies},
                )
            name, keys = result.name, result.keys
        except Exception as exc:
            logger.error("Mellea curate API error: %s", exc)
            name, keys = "Curator's Pick", []

        keys = _pick(keys, movie_seen, max_results)
        if keys:
            logger.info("Collection '%s' (%d/%d): %d items", name, i + 1, count, len(keys))
            client.upsert_collection(name, keys)
            collections_created_records.append({"title": name, "library": client._library_name})
            exclude_themes.append(name)
            collections_written.append((name, len(keys)))
            all_surfaced_keys.extend(keys)
        else:
            logger.warning("Collection '%s' (%d/%d) had no items — skipping", name, i + 1, count)

    # 8. Atomically persist state and cooldown
    save_curator_state(collections_created_records, state_path)
    save_cooldown(cooldown_history, all_surfaced_keys, cooldown_path)

    for name, cnt in collections_written:
        logger.info("  ✓ %s (%d items)", name, cnt)
    logger.info("Done — %d collections updated and pinned to library views.", len(collections_written))

    return {
        "collections_written": collections_written,
        "records": collections_created_records,
        "surfaced_keys": all_surfaced_keys,
    }


# Aliases for backward compatibility
start_session = ai_session


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    """Build and parse CLI argument parser for curator job."""
    parser = argparse.ArgumentParser(description="Projectionist AI Curator Job")
    parser.add_argument(
        "--config",
        dest="config_path",
        default=None,
        help="Path to configuration file (default: config.yaml)",
    )
    parser.add_argument(
        "positional_config",
        nargs="?",
        default=None,
        help=argparse.SUPPRESS,
    )
    return parser.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> None:
    """CLI runner entrypoint for curator job."""
    args = parse_args(argv)
    cfg_path = args.config_path or args.positional_config
    run(config_path=cfg_path)


if __name__ == "__main__":
    main()
